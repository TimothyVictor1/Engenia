"""Kort-nozzle-like thruster duct for the thruster_duct product pack.

A printed PETG ring that surrounds a small thruster propeller: a flared inlet that contracts to a
short straight throat at the propeller plane, then a gentle diffuser to the exit, with a bolted
mounting flange at the exit end. The profile (length over diameter, propeller-plane position,
throat land, inlet and diffuser angles) comes from the pack's `duct:` rules; the throat diameter
is the propeller plus the tip clearance the requirement allows.

`propose` sizes every dimension from the requirements (propeller, clearance limits, flange bolts,
thrust), the contract and the pack; `build` revolves the profile and drills the bolt circle. The
mechanical interface publishes the throat diameter (prop_clearance reads it) and the flange root
section (bending_stress reads it, with the thrust from the requirement). All lengths are mm; the
duct axis is z, the inlet at z = 0 and the flange at the exit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from build123d import (
    Axis,
    BuildLine,
    BuildPart,
    BuildSketch,
    Cylinder,
    GeomType,
    Plane,
    Polyline,
    Pos,
    Shape,
    fillet,
    make_face,
    revolve,
)

from engenia.contracts.models import Contract, Requirement
from engenia.sim.duct import parse_bolts, parse_propeller_mm, parse_thrust_n
from engenia.sim.loads import scenario_extra_max, scenario_load_factor

EPS = 1e-3


class DuctEnvelopeError(ValueError):
    """The duct does not fit the contract's mechanical envelope."""

    def __init__(self, required: list[float], available: list[float]) -> None:
        self.required = required
        self.available = available
        super().__init__(f"The duct needs an envelope of {required} mm; available {available} mm")


@dataclass(frozen=True)
class Params:
    propeller_mm: float
    clearance_mm: float  # nominal tip clearance, (throat - propeller) / 2
    throat_diameter_mm: float
    wall_mm: float
    length_mm: float
    plane_mm: float  # propeller plane, from the inlet
    land_mm: float  # straight throat land, centred on the plane
    inlet_angle_deg: float
    diffuser_angle_deg: float
    flange_thickness_mm: float
    flange_width_mm: float  # radial width beyond the wall
    bolt_count: int
    bolt_mm: float
    bolt_hole_mm: float
    lip_mm: float
    fillet_mm: float
    design_thrust_n: float = 0.0
    infill_factor: float = 1.0

    def __post_init__(self) -> None:
        numbers = [self.propeller_mm, self.clearance_mm, self.throat_diameter_mm, self.wall_mm,
                   self.length_mm, self.plane_mm, self.land_mm, self.flange_thickness_mm,
                   self.flange_width_mm, self.bolt_mm, self.bolt_hole_mm]
        if any(not math.isfinite(v) or v <= 0 for v in numbers) or self.bolt_count < 3:
            raise ValueError("Dimensions must be finite and positive, with at least 3 bolts")
        if not 0 <= self.lip_mm < self.wall_mm / 2 or not 0 <= self.fillet_mm < self.wall_mm * 2:
            raise ValueError("lip and fillet radii must fit the wall")
        if not 0 < self.infill_factor <= 1:
            raise ValueError("Invalid infill factor")
        if not math.isclose(self.throat_diameter_mm, self.propeller_mm + 2 * self.clearance_mm,
                            abs_tol=1e-6):
            raise ValueError("throat diameter must be the propeller plus two tip clearances")
        flange_start = self.length_mm - self.flange_thickness_mm
        if self.land_start_mm <= EPS or self.land_end_mm >= flange_start:
            raise ValueError("the throat land does not fit between the inlet and the flange")
        if self.bolt_hole_mm <= self.bolt_mm:
            raise ValueError("bolt hole must clear the bolt")
        if self.flange_width_mm < self.bolt_hole_mm:
            raise ValueError("flange is too narrow for its bolt holes")
        if self.flange_thickness_mm < self.wall_mm - EPS:
            raise ValueError("flange is thinner than the wall")
        if self.bolt_count * self.bolt_hole_mm >= 2 * math.pi * self.bolt_circle_radius_mm:
            raise ValueError("the bolt holes do not fit around the flange")

    @property
    def land_start_mm(self) -> float:
        return self.plane_mm - self.land_mm / 2

    @property
    def land_end_mm(self) -> float:
        return self.plane_mm + self.land_mm / 2

    @property
    def throat_radius_mm(self) -> float:
        return self.throat_diameter_mm / 2

    @property
    def exit_inner_radius_mm(self) -> float:
        return self.throat_radius_mm + (self.length_mm - self.land_end_mm) * math.tan(
            math.radians(self.diffuser_angle_deg))

    @property
    def inlet_inner_radius_mm(self) -> float:
        return self.throat_radius_mm + self.land_start_mm * math.tan(
            math.radians(self.inlet_angle_deg))

    @property
    def exit_outer_radius_mm(self) -> float:
        """Outside of the wall where it meets the flange."""
        return self._outer_radius(self.length_mm - self.flange_thickness_mm)

    @property
    def flange_outer_radius_mm(self) -> float:
        return self.exit_outer_radius_mm + self.flange_width_mm

    @property
    def bolt_circle_radius_mm(self) -> float:
        return self.exit_outer_radius_mm + self.flange_width_mm / 2

    def _outer_radius(self, z: float) -> float:
        """Outer surface: the inner surface plus the wall, measured radially."""
        if z <= self.land_start_mm:
            inner = self.throat_radius_mm + (self.land_start_mm - z) * math.tan(
                math.radians(self.inlet_angle_deg))
        elif z <= self.land_end_mm:
            inner = self.throat_radius_mm
        else:
            inner = self.throat_radius_mm + (z - self.land_end_mm) * math.tan(
                math.radians(self.diffuser_angle_deg))
        return inner + self.wall_mm


# --- proposal --------------------------------------------------------------------------------


def _round_up(value: float, step: float) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 6)


def _decision(title, choice, reason, requirements=(), **numbers) -> dict:
    return {"title": title, "choice": choice, "reason": reason,
            "requirements": list(requirements), "numbers": numbers}


def propose(
    contract: Contract, requirements: list[Requirement], pack: dict, pack_dir: Path | None = None
) -> tuple[dict, list[dict]]:
    """The duct's parameters and one decision record per design choice. Pure function."""
    cfg, flange_cfg, material = pack["duct"], pack["duct"]["flange"], pack["structure"]
    step, grid = cfg["step_mm"], cfg["clearance_grid_mm"]
    approved = [r for r in requirements if r.status == "approved"]

    # Propeller and tip clearance from the prop_clearance requirement.
    clear = [(parse_propeller_mm(r.statement), r) for r in approved
             if r.verify.check == "prop_clearance"]
    clear = [(d, r) for d, r in clear if d is not None]
    if not clear:
        raise ValueError(
            "a duct needs an approved prop_clearance requirement whose statement names the "
            "propeller, for example 'fit a 70 mm propeller with 1 to 2 mm tip clearance'"
        )
    prop, governing = max(clear, key=lambda pair: pair[0])
    lows = [r.verify.limit.min for _, r in clear if r.verify.limit and r.verify.limit.min
            is not None]
    highs = [r.verify.limit.max for _, r in clear if r.verify.limit and r.verify.limit.max
             is not None]
    low, high = max(lows, default=None), min(highs, default=None)
    if low is None and high is None:
        raise ValueError(f"{governing.id}: prop_clearance needs a min or a max limit")
    shift = scenario_extra_max(pack_dir, "prop_clearance", "clearance_shift_mm", 0.0) \
        if pack_dir is not None else 0.0
    margin = cfg["clearance_margin_mm"]
    if low is not None and high is not None:
        target = (low + shift + high) / 2
    elif low is not None:
        target = low + shift + margin
    else:
        target = high - margin
    clearance = round(round(target / grid) * grid, 6)
    if (low is not None and clearance - shift < low - EPS) or (
            high is not None and clearance > high + EPS) or clearance <= 0:
        raise ValueError(
            f"no tip clearance meets {governing.id}: {low}..{high} mm with a {shift:g} mm "
            "swelling allowance"
        )
    throat = round(prop + 2 * clearance, 6)
    bounds = f"{low if low is not None else '-'} to {high if high is not None else '-'}"
    decisions = [_decision(
        "Tip clearance", f"{clearance:g} mm",
        f"{governing.id} allows {bounds} mm; the pack's scenarios shift the throat in by "
        f"{shift:g} mm, so the clearance is the middle of what is left ({low or 0:g} + {shift:g} "
        f"to {high if high is not None else '-'} mm)" if low is not None and high is not None
        else f"{governing.id} allows {bounds} mm; aim {margin:g} mm inside the single bound, "
        f"after the {shift:g} mm swelling shift",
        [governing.id], clearance_mm=clearance, shift_mm=shift,
    ), _decision(
        "Throat diameter", f"{throat:g} mm",
        f"the {prop:g} mm propeller plus two tip clearances of {clearance:g} mm",
        [governing.id], throat_mm=throat, propeller_mm=prop,
    )]

    # Wall.
    wall_reqs = [(r.verify.limit.min, r) for r in approved
                 if r.verify.check == "wall_thickness_min" and r.verify.limit is not None
                 and r.verify.limit.min is not None]
    wall = _round_up(max([cfg["min_wall_mm"], *(v for v, _ in wall_reqs)]), step)
    decisions.append(_decision(
        "Wall thickness", f"{wall:g} mm",
        f"the larger of the pack's {cfg['min_wall_mm']:g} mm and the wall requirements, rounded "
        f"up to a whole {step:g} mm line width",
        sorted({r.id for _, r in wall_reqs}), wall_mm=wall,
    ))

    # Profile.
    length = round(cfg["length_ratio"] * throat, 6)
    plane = round(cfg["plane_ratio"] * length, 6)
    land = round(cfg["land_ratio"] * prop, 6)
    decisions.append(_decision(
        "Nozzle profile", f"{length:g} mm long, propeller plane {plane:g} mm from the inlet",
        f"length is {cfg['length_ratio']:g} x the throat (Kort-nozzle-like), the plane "
        f"{cfg['plane_ratio']:g} of the way along, a {land:g} mm straight land "
        f"({cfg['land_ratio']:g} x the propeller), {cfg['inlet_angle_deg']:g} deg inlet and "
        f"{cfg['diffuser_angle_deg']:g} deg diffuser half-angles",
        [governing.id], length_mm=length, plane_mm=plane, land_mm=land,
    ))

    # Flange: bolts from a requirement ("4 x M4") else the pack's, thickness from the thrust.
    bolts = next((b for r in approved if (b := parse_bolts(r.statement)) is not None), None)
    count = bolts.count if bolts else int(flange_cfg["default_bolts"])
    size = bolts.size_mm if bolts else float(flange_cfg["default_bolt_mm"])
    hole = round(size + flange_cfg["bolt_clearance_mm"], 6)
    width = _round_up(hole + 2 * flange_cfg["edge_ratio"] * hole, step)
    # Geometry the flange stress depends on, from a trial duct at the minimum flange thickness.
    thick = _round_up(max(wall, flange_cfg["min_thickness_ratio"] * size), step)
    trial = dict(
        propeller_mm=prop, clearance_mm=clearance, throat_diameter_mm=throat, wall_mm=wall,
        length_mm=length, plane_mm=plane, land_mm=land,
        inlet_angle_deg=cfg["inlet_angle_deg"], diffuser_angle_deg=cfg["diffuser_angle_deg"],
        flange_thickness_mm=thick, flange_width_mm=width, bolt_count=count, bolt_mm=size,
        bolt_hole_mm=hole, lip_mm=0.0, fillet_mm=0.0,
    )
    thrusts = [(parse_thrust_n(r.statement), r) for r in approved
               if r.verify.check == "bending_stress" and parse_thrust_n(r.statement)]
    reason = (f"the minimum for {size:g} mm bolts ({flange_cfg['min_thickness_ratio']:g} x the "
              f"bolt, never below the wall)")
    design_thrust, flange_reqs = 0.0, []
    if thrusts:
        thrust, req = max(thrusts, key=lambda pair: pair[0])
        factor = scenario_load_factor(pack_dir, "bending_stress") if pack_dir else 1.0
        limits = [r.verify.limit.min for _, r in thrusts
                  if r.verify.limit is not None and r.verify.limit.min is not None]
        required_sf = max(limits, default=1.0) * cfg["stress_margin"]
        design_thrust, flange_reqs = thrust * factor, [req.id]
        probe = Params(**trial)
        lever = probe.flange_width_mm / 2
        b_net = 2 * math.pi * probe.bolt_circle_radius_mm - count * hole
        allowable = float(material["allowable_stress_mpa"]) * float(
            material["printed_knock_down_factor"])
        needed = math.sqrt(6 * design_thrust * lever * required_sf / (b_net * allowable))
        thick = _round_up(max(thick, needed), step)
        reason = (f"{thrust:g} N of thrust x the scenario factor {factor:g} acts "
                  f"{lever:g} mm from the bolt line over {b_net:.1f} mm of net section: stress "
                  f"needs {needed:.2f} mm at safety factor {required_sf:g}, so {thick:g} mm")
    circle = 2 * Params(**{**trial, "flange_thickness_mm": thick}).bolt_circle_radius_mm
    decisions.append(_decision(
        "Mounting flange", f"{count} x M{size:g} on a {circle:.1f} mm circle, {thick:g} mm thick",
        f"{'the requirement names' if bolts else 'the pack default is'} {count} x M{size:g}; "
        f"the clearance hole is {hole:g} mm and the flange {width:g} mm wide; thickness is "
        + reason, flange_reqs or [governing.id], flange_thickness_mm=thick,
        flange_width_mm=width, hole_mm=hole, bolts=count, design_thrust_n=design_thrust,
    ))

    lip = round(min(cfg["lip_ratio"] * wall, wall / 2 - 0.05), 2)
    fillet_r = round(min(flange_cfg["fillet_ratio"] * wall, 2 * wall - 0.05), 2)
    decisions.append(_decision(
        "Fillets", f"{lip:g} mm lip, {fillet_r:g} mm at the flange",
        f"the lip rounds the inlet edge ({cfg['lip_ratio']:g} x the wall) and the flange fillet "
        f"({flange_cfg['fillet_ratio']:g} x the wall) spreads the thrust into the wall",
        [], lip_mm=lip, fillet_mm=fillet_r,
    ))
    params = {**trial, "flange_thickness_mm": thick, "lip_mm": lip, "fillet_mm": fillet_r,
              "design_thrust_n": round(design_thrust, 6), "infill_factor": 1.0}
    p = Params(**params)
    dims = [2 * p.flange_outer_radius_mm, 2 * p.flange_outer_radius_mm, p.length_mm]
    envelope = list(contract.budgets.mechanical.outer_envelope_mm)
    if any(d > e for d, e in zip(sorted(dims), sorted(envelope), strict=True)):
        raise DuctEnvelopeError([round(d, 6) for d in sorted(dims)], envelope)
    decisions.append(_decision(
        "Overall size", " x ".join(f"{d:.1f}" for d in dims) + " mm",
        f"inside the {envelope[0]:g} x {envelope[1]:g} x {envelope[2]:g} mm envelope "
        f"(the print bed is {pack['printing']['bed_mm']} mm)", [],
        diameter_mm=round(dims[0], 3), length_mm=p.length_mm,
    ))
    return params, decisions


# --- geometry --------------------------------------------------------------------------------


def _circle_edges(shape: Shape, radius: float, z: float) -> list:
    return [e for e in shape.edges()
            if e.geom_type == GeomType.CIRCLE and abs(e.radius - radius) < EPS
            and abs(e.center().Z - z) < EPS]


def build(params: Params) -> dict[str, Shape]:
    """The printed duct as one part: the revolved profile, the bolt circle and the fillets."""
    p = params
    z1, z2, length, t = p.land_start_mm, p.land_end_mm, p.length_mm, p.wall_mm
    zf = length - p.flange_thickness_mm
    r_t, r_f = p.throat_radius_mm, p.flange_outer_radius_mm
    outer_inlet = p.inlet_inner_radius_mm + t
    profile = [
        (p.inlet_inner_radius_mm, 0.0), (outer_inlet, 0.0),
        (r_t + t, z1), (r_t + t, z2), (p.exit_outer_radius_mm, zf), (r_f, zf), (r_f, length),
        (p.exit_inner_radius_mm, length), (r_t, z2), (r_t, z1),
    ]
    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline(*profile, close=True)
            make_face()
        revolve(axis=Axis.Z)
    duct = part.part
    for k in range(p.bolt_count):
        angle = 2 * math.pi * k / p.bolt_count
        duct -= Pos(p.bolt_circle_radius_mm * math.cos(angle),
                    p.bolt_circle_radius_mm * math.sin(angle), length - p.flange_thickness_mm / 2
                    ) * Cylinder(p.bolt_hole_mm / 2, p.flange_thickness_mm + 2)
    if p.lip_mm > 0:
        duct = fillet(_circle_edges(duct, p.inlet_inner_radius_mm, 0.0)
                      + _circle_edges(duct, outer_inlet, 0.0), p.lip_mm)
    if p.fillet_mm > 0:
        duct = fillet(_circle_edges(duct, p.exit_outer_radius_mm, zf), p.fillet_mm)
    duct.label = "duct"
    if not duct.is_valid or len(duct.solids()) != 1:
        raise ValueError("The duct did not produce one connected solid")
    return {"duct": duct}


def analysis_solids(params: Params) -> dict[str, Shape]:
    """No analysis geometry: the flange check is a beam formula on the bolt-line section."""
    return {}


def hardware(params: Params) -> dict[str, float]:
    """No bought parts counted: the flange bolts belong to the thruster mount."""
    return {}


def extra_facts(params: Params) -> dict:
    p = params
    # The flange is a ring supported at its bolts and loaded at the wall: per bolt, the thrust
    # share acts one half flange width from the bolt line over the net section between holes.
    # All bolts together are a beam of width (circumference - holes) and thickness h.
    net_width = 2 * math.pi * p.bolt_circle_radius_mm - p.bolt_count * p.bolt_hole_mm
    return {
        "wall_min_mm": round(min(p.wall_mm, p.flange_thickness_mm), 6),
        "duct_inner_diameter_mm": p.throat_diameter_mm,
        "duct_exit_diameter_mm": round(2 * p.exit_inner_radius_mm, 6),
        "duct_inlet_diameter_mm": round(2 * p.inlet_inner_radius_mm, 6),
        "duct_length_mm": p.length_mm,
        "propeller_plane_mm": p.plane_mm,
        "propeller_mm": p.propeller_mm,
        "tip_clearance_mm": p.clearance_mm,
        "flange_outer_diameter_mm": round(2 * p.flange_outer_radius_mm, 6),
        "bolt_circle_diameter_mm": round(2 * p.bolt_circle_radius_mm, 6),
        "flange_bolts": p.bolt_count,
        "bending_root_section_mm": [round(net_width, 6), p.flange_thickness_mm],
        "bending_root_offset_mm": 0.0,
        "bending_lever_mm": round(p.flange_width_mm / 2, 6),
    }
