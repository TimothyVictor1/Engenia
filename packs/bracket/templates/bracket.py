"""Printed PETG brackets for the bracket product pack: a mast clamp and an L-bracket.

Both hold a point load on a cantilever. The mast clamp is a split ring around a mast with a
rectangular arm that carries a four-hole mounting plate at the load point; the L-bracket is a
wall leg and a shelf with an inside fillet and mounting holes in both legs. `propose` picks the
style from the requirement (a mast diameter in the statement means a clamp) and sizes every
dimension from the load, the safety factor, the wall requirement and the pack's rules.

The cantilever root section (`bending_root_section_mm`, `bending_root_offset_mm`) is published as
mechanical facts; the bending_stress verifier reads it and the load from the requirement, so the
design and the check use the same load. All lengths are millimetres.

Coordinates: a mast clamp has the mast on the z axis and the arm along +x; an L-bracket has the
wall at x = 0 and the shelf along +x.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from build123d import Align, Box, Cylinder, Pos, Rot, Shape, fillet

from engenia.contracts.models import Contract, Requirement
from engenia.sim.loads import parse_load, scenario_load_factor

BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)
STYLES = ("mast_clamp", "l_bracket")
EPS = 1e-3


class BracketEnvelopeError(ValueError):
    """The bracket does not fit the contract's mechanical envelope."""

    def __init__(self, required: list[float], available: list[float]) -> None:
        self.required = required
        self.available = available
        super().__init__(
            f"The bracket needs an envelope of {required} mm; available {available} mm"
        )


@dataclass(frozen=True)
class Params:
    style: str
    lever_mm: float  # datum (mast axis or wall face) to the load
    load_n: float
    arm_width_mm: float  # b: across the bending direction
    arm_depth_mm: float  # h: in the bending direction
    plate_size_mm: float  # clamp: the square arm plate; L-bracket: unused
    plate_thickness_mm: float  # clamp: arm plate; L-bracket: wall leg
    hole_pitch_mm: float
    hole_diameter_mm: float
    fillet_mm: float
    mast_diameter_mm: float = 0.0
    bore_diameter_mm: float = 0.0
    ring_wall_mm: float = 0.0
    ring_height_mm: float = 0.0
    slit_mm: float = 0.0
    ear_length_mm: float = 0.0
    clamp_bolt_hole_mm: float = 0.0
    clamp_bolt_mass_g: float = 0.0
    base_height_mm: float = 0.0
    infill_factor: float = 1.0

    def __post_init__(self) -> None:
        if self.style not in STYLES:
            raise ValueError(f"style must be one of {STYLES}")
        required = [self.lever_mm, self.load_n, self.arm_width_mm, self.arm_depth_mm,
                    self.plate_thickness_mm, self.hole_pitch_mm, self.hole_diameter_mm,
                    self.fillet_mm]
        if self.style == "mast_clamp":
            required += [self.mast_diameter_mm, self.bore_diameter_mm, self.ring_wall_mm,
                         self.ring_height_mm, self.slit_mm, self.ear_length_mm,
                         self.clamp_bolt_hole_mm, self.plate_size_mm]
        else:
            required += [self.base_height_mm]
        if any(not math.isfinite(v) or v <= 0 for v in required):
            raise ValueError("Dimensions must be finite and positive")
        if not 0 < self.infill_factor <= 1:
            raise ValueError("Invalid infill factor")
        if 2 * self.fillet_mm >= min(self.arm_width_mm, self.arm_depth_mm) + EPS:
            raise ValueError("fillet is too large for the arm section")
        if self.style == "mast_clamp":
            if self.bore_diameter_mm < self.mast_diameter_mm:
                raise ValueError("clamp bore is smaller than the mast")
            if self.arm_depth_mm > self.ring_height_mm + EPS:
                raise ValueError("arm is taller than the clamp ring")
            if self.arm_length_mm <= 0:
                raise ValueError("load point is inside the clamp")
            if self.plate_size_mm < self.hole_pitch_mm + self.hole_diameter_mm:
                raise ValueError("plate is too small for its holes")
        elif self.arm_width_mm < self.hole_pitch_mm + self.hole_diameter_mm:
            raise ValueError("shelf is too narrow for its holes")

    @property
    def ring_outer_radius_mm(self) -> float:
        if self.style != "mast_clamp":
            return 0.0
        return self.bore_diameter_mm / 2 + self.ring_wall_mm

    @property
    def arm_length_mm(self) -> float:
        """Visible arm: from the ring surface (or the wall face) to the plate (or the load)."""
        if self.style == "mast_clamp":
            return self.lever_mm - self.plate_thickness_mm - self.ring_outer_radius_mm
        return self.lever_mm

    @property
    def root_offset_mm(self) -> float:
        return self.ring_outer_radius_mm


# --- proposal --------------------------------------------------------------------------------


def _round_up(value: float, step: float) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 6)


def _decision(title, choice, reason, requirements=(), **numbers) -> dict:
    return {"title": title, "choice": choice, "reason": reason,
            "requirements": list(requirements), "numbers": numbers}


def propose(
    contract: Contract, requirements: list[Requirement], pack: dict, pack_dir: Path | None = None
) -> tuple[dict, list[dict]]:
    """The bracket's parameters and one decision record per design choice.

    Pure function: the load, lever, safety factor and mast come from the bending_stress
    requirement's statement and limit, the wall from wall_thickness_min requirements, the
    envelope and mass from the contract, everything else from the pack's `bracket:` rules.
    """
    cfg, material = pack["bracket"], pack["structure"]
    mount, clamp = cfg["mount"], cfg["clamp"]
    step = cfg["step_mm"]
    approved = [r for r in requirements if r.status == "approved"]
    loads = [(load, r) for r in approved if r.verify.check == "bending_stress"
             if (load := parse_load(r.statement)) is not None]
    if not loads:
        raise ValueError(
            "a bracket needs an approved bending_stress requirement whose statement names the "
            "load and its distance, for example 'hold a 0.5 kg load 150 mm from the mast axis'"
        )
    load, governing = max(loads, key=lambda pair: pair[0].force_n * pair[0].lever_mm)
    limit_sf = max((r.verify.limit.min for _, r in loads
                    if r.verify.limit is not None and r.verify.limit.min is not None),
                   default=1.0)
    # The pack's red-team scenarios (a gust, ice) multiply the load; the verifier applies the
    # same factor, so the arm is sized for the load it will be judged under.
    factor = scenario_load_factor(pack_dir, "bending_stress") if pack_dir is not None else 1.0
    required_sf = limit_sf * factor
    wall_reqs = [(r.verify.limit.min, r) for r in approved
                 if r.verify.check == "wall_thickness_min" and r.verify.limit is not None
                 and r.verify.limit.min is not None]
    section_min = _round_up(max([cfg["min_wall_mm"], *(v for v, _ in wall_reqs)]), step)
    allowable = float(material["allowable_stress_mpa"]) * float(
        material["printed_knock_down_factor"]
    )
    style = "mast_clamp" if load.mast_mm else "l_bracket"
    margin = cfg["stress_margin"]
    hole, pitch = float(mount["hole_mm"]), float(mount["pitch_mm"])
    edge = mount["edge_ratio"] * hole
    wall_ids = sorted({r.id for _, r in wall_reqs})
    decisions = [_decision(
        "Style", style.replace("_", " "),
        (f"the requirement names a {load.mast_mm:g} mm mast, so the bracket clamps it"
         if load.mast_mm else "the requirement names no mast, so the bracket is an L-bracket"),
        [governing.id], load_n=round(load.force_n, 4), lever_mm=load.lever_mm,
    ), _decision(
        "Design load", f"{load.force_n * factor:.2f} N",
        f"{load.force_n:.2f} N from the requirement, times the red-team load factor {factor:g}"
        " from the pack's scenarios",
        [governing.id], load_n=round(load.force_n, 4), load_factor=factor,
        safety_factor=limit_sf,
    )]
    ring = {}
    if style == "mast_clamp":
        bore = _round_up(load.mast_mm + clamp["fit_clearance_mm"], 0.1)
        ring_wall = section_min
        ring_height = _round_up(max(clamp["min_height_mm"], clamp["height_ratio"]
                                    * load.mast_mm), step)
        outer = bore / 2 + ring_wall
        root = outer
        depth = ring_height
        plate_t = section_min
        plate = _round_up(pitch + 2 * edge, step)
        ring = {
            "mast_diameter_mm": load.mast_mm, "bore_diameter_mm": bore,
            "ring_wall_mm": ring_wall, "ring_height_mm": ring_height,
            "slit_mm": float(clamp["slit_mm"]), "ear_length_mm": float(clamp["ear_length_mm"]),
            "clamp_bolt_hole_mm": float(clamp["bolt_hole_mm"]),
            "clamp_bolt_mass_g": float(clamp["bolt_mass_g"]),
        }
        decisions.append(_decision(
            "Clamp diameter", f"{bore:g} mm bore",
            f"the {load.mast_mm:g} mm mast plus {clamp['fit_clearance_mm']:g} mm printed fit "
            f"clearance; the ring is {ring_wall:g} mm thick and {ring_height:g} mm tall "
            f"({clamp['height_ratio']:g} x the mast, at least {clamp['min_height_mm']:g} mm)",
            wall_ids, bore_mm=bore, ring_wall_mm=ring_wall, ring_height_mm=ring_height,
        ))
    else:
        root, plate = 0.0, 0.0
        depth = None
        plate_t = section_min
    lever_arm = load.lever_mm - root
    if lever_arm <= plate_t + (0.0 if style == "l_bracket" else 1.0):
        raise ValueError(
            f"the load is {load.lever_mm:g} mm from the datum, inside the {root:g} mm clamp"
        )
    moment = load.force_n * lever_arm  # N mm at the root section
    if style == "mast_clamp":
        width = _round_up(max(6 * moment * required_sf * margin / (depth**2 * allowable),
                              depth / cfg["arm"]["max_aspect"], section_min), step)
        b_text = (f"width {width:g} mm: stress needs "
                  f"{6 * moment * required_sf * margin / (depth**2 * allowable):.2f} mm at "
                  f"safety factor {required_sf:g} x {margin:g}, the aspect limit "
                  f"{cfg['arm']['max_aspect']:g} needs {depth / cfg['arm']['max_aspect']:.2f} mm, "
                  f"the wall minimum is {section_min:g} mm")
        arm_b, arm_h = width, depth
    else:
        shelf_width = _round_up(max(cfg["shelf"]["width_mm"], pitch + 2 * edge), step)
        thickness = _round_up(max(
            math.sqrt(6 * moment * required_sf * margin / (shelf_width * allowable)),
            section_min), step)
        b_text = (f"thickness {thickness:g} mm on a {shelf_width:g} mm wide shelf: stress needs "
                  f"{math.sqrt(6 * moment * required_sf * margin / (shelf_width * allowable)):.2f}"
                  f" mm at safety factor {required_sf:g} x {margin:g}, the wall minimum is "
                  f"{section_min:g} mm")
        arm_b, arm_h = shelf_width, thickness
        plate_t = max(plate_t, thickness)
    sigma = 6 * moment / (arm_b * arm_h**2)
    decisions.append(_decision(
        "Arm section", f"{arm_b:g} x {arm_h:g} mm",
        f"{b_text}; root stress {sigma:.2f} MPa against {allowable:g} MPa allowable "
        f"(safety factor {allowable / sigma:.2f})",
        [governing.id, *wall_ids], moment_n_mm=round(moment, 3), sigma_mpa=round(sigma, 4),
        allowable_mpa=allowable, safety_factor=round(allowable / sigma, 4),
    ))
    fillet_r = round(min(cfg["fillet_mm"], 0.4 * min(arm_b, arm_h)), 1)
    decisions.append(_decision(
        "Fillet", f"{fillet_r:g} mm",
        f"the pack's {cfg['fillet_mm']:g} mm, or 0.4 x the thinnest arm section when that is "
        "smaller, so the fillet fits",
        [], fillet_mm=fillet_r,
    ))
    decisions.append(_decision(
        "Mounting holes", f"4 x {hole:g} mm on a {pitch:g} mm square" if style == "mast_clamp"
        else f"2 + 2 x {hole:g} mm, {pitch:g} mm apart",
        f"clearance for {mount['bolt_mm']:g} mm bolts; the plate keeps {edge:g} mm of material "
        "from each hole to its edge", [], hole_mm=hole, pitch_mm=pitch,
    ))
    params = {
        "style": style, "lever_mm": load.lever_mm, "load_n": round(load.force_n, 6),
        "arm_width_mm": arm_b, "arm_depth_mm": arm_h, "plate_size_mm": plate,
        "plate_thickness_mm": plate_t, "hole_pitch_mm": pitch, "hole_diameter_mm": hole,
        "fillet_mm": fillet_r, "infill_factor": 1.0, **ring,
    }
    if style == "l_bracket":
        params["base_height_mm"] = _round_up(max(cfg["shelf"]["base_height_mm"],
                                                 pitch + 2 * edge), step)
    p = Params(**params)
    if style == "mast_clamp":
        dims = [p.lever_mm + p.ring_outer_radius_mm + p.ear_length_mm,
                max(p.plate_size_mm, 2 * p.ring_outer_radius_mm),
                max(p.plate_size_mm, p.ring_height_mm)]
    else:
        dims = [p.lever_mm + p.plate_thickness_mm, p.arm_width_mm,
                p.base_height_mm]
    envelope = list(contract.budgets.mechanical.outer_envelope_mm)
    if any(d > e for d, e in zip(sorted(dims), sorted(envelope), strict=True)):
        raise BracketEnvelopeError([round(d, 6) for d in sorted(dims)], envelope)
    decisions.append(_decision(
        "Overall size", " x ".join(f"{d:g}" for d in dims) + " mm",
        f"inside the {envelope[0]:g} x {envelope[1]:g} x {envelope[2]:g} mm envelope "
        f"(the print bed is {pack['printing']['bed_mm']} mm)", [],
        length_mm=dims[0], width_mm=dims[1], height_mm=dims[2],
    ))
    return params, decisions


# --- geometry --------------------------------------------------------------------------------


def _edges_where(shape: Shape, keep) -> list:
    return [edge for edge in shape.edges() if keep(edge.center())]


def _holes_x(shape: Shape, x: float, thickness: float, points, diameter: float) -> Shape:
    for y, z in points:
        shape -= Pos(x, y, z) * Rot(Y=90) * Cylinder(diameter / 2, thickness + 2)
    return shape


def _mast_clamp(p: Params) -> Shape:
    r_b, r_o, w = p.bore_diameter_mm / 2, p.ring_outer_radius_mm, p.ring_height_mm
    zc, b, h, t = w / 2, p.arm_width_mm, p.arm_depth_mm, p.plate_thickness_mm
    ring = Cylinder(r_o, w, align=BOTTOM) - Cylinder(r_b, w, align=BOTTOM)
    # Split ring: a slit on the -x side, flanked by two ears that carry the clamp bolt.
    ring -= Pos(-(r_b + p.ring_wall_mm / 2), 0, zc) * Box(p.ring_wall_mm + 4, p.slit_mm, w + 2)
    ear_t = p.ring_wall_mm
    x_far, x_near = -(r_o + p.ear_length_mm), -(r_o - 1.0)
    for side in (-1, 1):
        ring += Pos((x_far + x_near) / 2, side * (p.slit_mm / 2 + ear_t / 2), zc) * Box(
            x_near - x_far, ear_t, w)
    bolt_x = x_far + p.ear_length_mm / 2 + 0.5
    ring -= Pos(bolt_x, 0, zc) * Rot(X=90) * Cylinder(
        p.clamp_bolt_hole_mm / 2, p.slit_mm + 2 * ear_t + 2)
    # Arm and plate.
    x0, x1 = r_o - p.ring_wall_mm / 2, p.lever_mm - t
    body = ring + Pos((x0 + x1) / 2, 0, zc) * Box(x1 - x0, b, h)
    plate = Pos(p.lever_mm - t / 2, 0, zc) * Box(t, p.plate_size_mm, p.plate_size_mm)
    half = p.hole_pitch_mm / 2
    plate = _holes_x(plate, p.lever_mm - t / 2, t, [(sy * half, zc + sz * half)
                                                   for sy in (-1, 1) for sz in (-1, 1)],
                     p.hole_diameter_mm)
    body += plate
    # Fillets: the arm where it meets the plate (all four sides) and the ring (two sides).
    body = fillet(_edges_where(body, lambda c: abs(c.X - x1) < EPS and abs(c.Y) <= b / 2 + EPS
                               and abs(c.Z - zc) <= h / 2 + EPS), p.fillet_mm)
    return fillet(
        _edges_where(body, lambda c: abs(c.Y) >= b / 2 - 0.2 and abs(c.Y) <= b / 2 + 0.2
                     and r_o - 0.5 <= c.X <= r_o and abs(c.Z - zc) <= h / 2 - 1.0
                     and abs(c.Z - zc) < h / 2 - EPS and c.X > 0),
        p.fillet_mm)


def _l_bracket(p: Params) -> Shape:
    length, w, h, t = p.lever_mm, p.arm_width_mm, p.arm_depth_mm, p.plate_thickness_mm
    shelf = Pos(length / 2, 0, h / 2) * Box(length, w, h)
    wall = Pos(-t / 2, 0, p.base_height_mm / 2) * Box(t, w, p.base_height_mm)
    body = shelf + wall
    body = fillet(_edges_where(body, lambda c: abs(c.X) < EPS and abs(c.Z - h) < EPS), p.fillet_mm)
    half = p.hole_pitch_mm / 2
    hole = p.hole_diameter_mm
    body = _holes_x(body, -t / 2, t, [(0, p.base_height_mm / 2 + s * half) for s in (-1, 1)], hole)
    edge = 1.5 * hole
    for s in (-1, 1):
        body -= Pos(length - edge - hole / 2, s * half, h / 2) * Cylinder(hole / 2, h + 2)
    return body


def build(params: Params) -> dict[str, Shape]:
    """The printed bracket as one part, in assembled position."""
    part = _mast_clamp(params) if params.style == "mast_clamp" else _l_bracket(params)
    part.label = "bracket"
    if not part.is_valid or len(part.solids()) != 1:
        raise ValueError("The bracket did not produce one connected solid")
    return {"bracket": part}


def analysis_solids(params: Params) -> dict[str, Shape]:
    """No separate analysis geometry: the bending check is a beam formula on the root section."""
    return {}


def hardware(params: Params) -> dict[str, float]:
    """Bought parts in grams: the clamp bolt and nut. The wall screws of an L-bracket and the
    antenna bolts belong to the installation and are not counted."""
    return {"clamp_bolt": params.clamp_bolt_mass_g} if params.style == "mast_clamp" else {}


def extra_facts(params: Params) -> dict:
    p = params
    walls = [p.arm_width_mm if p.style == "mast_clamp" else p.arm_depth_mm,
             p.plate_thickness_mm]
    if p.style == "mast_clamp":
        walls += [p.ring_wall_mm, p.arm_depth_mm]
    return {
        "style": p.style,
        "wall_min_mm": round(min(walls), 6),
        "bending_root_section_mm": [p.arm_width_mm, p.arm_depth_mm],
        "bending_root_offset_mm": round(p.root_offset_mm, 6),
        "load_lever_mm": p.lever_mm,
        "load_n": p.load_n,
        "arm_length_mm": round(p.arm_length_mm, 6),
        "mast_diameter_mm": p.mast_diameter_mm or None,
        "clamp_bore_mm": p.bore_diameter_mm or None,
        "fillet_mm": p.fillet_mm,
    }
