"""Watertight cylindrical sensor pod for the pressure_housing product pack.

A printed PETG tube closed by two plug end caps. Each cap has a flange that seats on the tube
end and a plug that slides into the bore; an O-ring in a groove on the plug seals it against the
bore (a static radial seal). External pressure pushes the caps further onto the tube, so the
flanges carry no tension. An optional cable-gland boss on the rear cap leads a sensor cable out.

The module is the whole product: `propose` chooses every dimension from the contract, the
requirements and the pack's rules (nothing here is a fixed size), `build` makes the printed
parts, `analysis_solids` makes the bonded vessel the structure solver meshes, `hardware` and
`extra_facts` give what the mechanical interface publishes. The O-ring comes from the standard
table in `parts/o_rings.toml`.

All lengths are millimetres. The pod axis is z; z = 0 is the lowest point of the assembly.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from build123d import Align, Axis, Cylinder, Pos, Rot, Shape, chamfer

from engenia.contracts.models import Contract, Requirement
from engenia.sim.depth import parse_depth_m, pressure_mpa

BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)
PACK_DIR = Path(__file__).resolve().parents[1]
TEMPLATE = "sensor_pod"
LEAD_IN_MM = 0.8  # chamfer on the free end of a plug so the O-ring does not catch.


class PodEnvelopeError(ValueError):
    """The pod does not fit the contract's mechanical envelope."""

    def __init__(self, required: list[float], available: list[float]) -> None:
        self.required = required
        self.available = available
        super().__init__(
            f"The pressure pod needs an envelope of {required} mm; available {available} mm"
        )


@dataclass(frozen=True)
class Params:
    outer_diameter_mm: float
    bore_diameter_mm: float
    wall_thickness_mm: float
    tube_length_mm: float
    flange_thickness_mm: float
    plug_depth_mm: float
    fit_gap_mm: float
    o_ring_id_mm: float
    o_ring_cs_mm: float
    groove_depth_mm: float
    groove_width_mm: float
    groove_land_mm: float
    battery_bay_mm: tuple[float, float, float]
    cable_gland: bool = False
    gland_hole_mm: float = 12.5
    boss_diameter_mm: float = 18.5
    boss_height_mm: float = 7.0
    gland_mass_g: float = 0.0
    design_depth_m: float = 0.0
    design_pressure_mpa: float = 0.0
    infill_factor: float = 1.0

    def __post_init__(self) -> None:
        numbers = (
            self.outer_diameter_mm, self.bore_diameter_mm, self.wall_thickness_mm,
            self.tube_length_mm, self.flange_thickness_mm, self.plug_depth_mm,
            self.o_ring_id_mm, self.o_ring_cs_mm, self.groove_depth_mm, self.groove_width_mm,
            self.groove_land_mm, *self.battery_bay_mm,
        )
        if len(self.battery_bay_mm) != 3 or any(
            not math.isfinite(v) or v <= 0 for v in numbers
        ):
            raise ValueError("Dimensions must be finite and positive")
        if self.fit_gap_mm < 0 or not math.isfinite(self.fit_gap_mm):
            raise ValueError("fit gap must be finite and not negative")
        if not 0 < self.infill_factor <= 1:
            raise ValueError("Invalid infill factor")
        if not math.isclose(self.outer_diameter_mm, self.bore_diameter_mm
                            + 2 * self.wall_thickness_mm, abs_tol=1e-6):
            raise ValueError("outer diameter must equal bore plus two walls")
        if self.plug_depth_mm < self.groove_width_mm + 2 * self.groove_land_mm - 1e-9:
            raise ValueError("plug is too short for the groove and its lands")
        if self.groove_depth_mm >= self.plug_od_mm / 2:
            raise ValueError("groove is deeper than the plug is wide")
        if self.cable_gland and self.boss_diameter_mm > self.bore_diameter_mm:
            raise ValueError("cable gland boss does not fit the end cap")
        if self.cable_gland and self.gland_hole_mm >= self.boss_diameter_mm:
            raise ValueError("gland hole leaves no boss wall")

    @property
    def plug_od_mm(self) -> float:
        return self.bore_diameter_mm - 2 * self.fit_gap_mm

    @property
    def groove_root_diameter_mm(self) -> float:
        return self.plug_od_mm - 2 * self.groove_depth_mm

    @property
    def cavity_length_mm(self) -> float:
        return self.tube_length_mm - 2 * self.plug_depth_mm

    @property
    def boss_offset_mm(self) -> float:
        return self.boss_height_mm if self.cable_gland else 0.0

    @property
    def total_length_mm(self) -> float:
        return self.tube_length_mm + 2 * self.flange_thickness_mm + self.boss_offset_mm


# --- the standard O-ring table ---------------------------------------------------------------


def load_o_rings(path: Path | None = None) -> tuple[list[tuple[float, float]], dict]:
    """(cross-section, inside diameter) pairs, smallest first, and the table's own settings."""
    with (path or PACK_DIR / "parts" / "o_rings.toml").open("rb") as stream:
        table = tomllib.load(stream)
    rings = sorted(
        (float(row["cs_mm"]), float(inside))
        for row in table["rings"] for inside in row["id_mm"]
    )
    return rings, table["table"]


def o_ring_mass_g(inside_mm: float, cs_mm: float, density_g_cm3: float) -> float:
    """Mass of a torus: cross-section area x centreline length x density."""
    volume_mm3 = math.pi * cs_mm**2 / 4 * math.pi * (inside_mm + cs_mm)
    return volume_mm3 / 1000 * density_g_cm3


# --- proposal --------------------------------------------------------------------------------


def _round_up(value: float, step: float) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 6)


def pack_scenarios(pack_dir: Path) -> tuple[float, float]:
    """(largest depth factor, largest water density in kg/m3) over the pack's red-team
    scenarios. A scenario that names neither leaves the design depth and 1005 kg/m3."""
    factor, density = 1.0, 1005.0
    for path in sorted((pack_dir / "scenarios").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        factor = max(factor, float((data.get("extra") or {}).get("depth_factor", 1.0)))
        density = max(density, float(data.get("water_density") or 0.0))
    return factor, density


def _buckling_wall_mm(bore: float, pressure: float, safety: float, modulus: float,
                      poisson: float, step: float) -> float:
    """Thinnest wall whose long-tube elastic collapse pressure, 2E/(1-v^2) (t/D)^3, is at least
    `safety` times the design pressure. Solved by stepping up, so the answer is on the grid."""
    wall = step
    while wall < 40.0:
        if 2 * modulus / (1 - poisson**2) * (wall / (bore + 2 * wall)) ** 3 >= safety * pressure:
            return wall
        wall += step
    raise ValueError("no wall up to 40 mm resists collapse at the design pressure")


def propose(
    contract: Contract, requirements: list[Requirement], pack: dict, pack_dir: Path
) -> tuple[dict, list[dict]]:
    """The pod's parameters and one decision record per design choice.

    Pure function of its arguments: the bay and envelope come from the contract, the wall
    minimum and the depth from the requirements, the pressure rules and the O-ring table from
    the pack. Returns (params for Params, decisions as dicts for events.emit_decision).
    """
    cfg, structure = pack["pod"], pack["structure"]
    ring_cfg = cfg["o_ring"]
    approved = [r for r in requirements if r.status == "approved"]
    decisions: list[dict] = []

    # 1. Bore: the bay's cross-section corners plus clearance, on a half-millimetre grid.
    length, width, height = contract.budgets.electronics.bay_mm
    diagonal = math.hypot(width, height)
    bore = _round_up(diagonal + 2 * cfg["bay_clearance_mm"], 0.5)

    # 2. O-ring: smallest cross-section whose standard ID stretches within the pack's limits.
    rings, table = load_o_rings(pack_dir / "parts" / "o_rings.toml")
    gap = cfg["fit_gap_mm"]
    chosen = None
    start = bore
    while chosen is None and bore <= start + 5.0:
        plug_od = bore - 2 * gap
        for cs, inside in rings:
            depth = (1 - ring_cfg["squeeze"]) * cs - gap
            stretch = (plug_od - 2 * depth - inside) / inside
            if ring_cfg["stretch_min"] <= stretch <= ring_cfg["stretch_max"]:
                chosen = (cs, inside, depth, stretch)
                break
        else:
            bore += 0.5
    if chosen is None:
        raise ValueError(
            f"no standard O-ring in parts/o_rings.toml fits a plug for a {start:g} mm bore"
        )
    cs, ring_id, groove_depth, stretch = chosen
    alternatives = [
        {"option": f"{c:g} mm cross-section",
         "why_not": ("no standard ID stretches within the pack's limits on this groove"
                     if c < cs else "heavier groove and ring than needed")}
        for c in sorted({c for c, _ in rings}) if c != cs
    ]
    why_bore = (f"bore {bore:g} mm clears the {width:g} x {height:g} mm bay cross-section "
                f"(corner diagonal {diagonal:.1f} mm) by {cfg['bay_clearance_mm']:g} mm a side")
    if bore != _round_up(diagonal + 2 * cfg["bay_clearance_mm"], 0.5):
        why_bore += "; widened until a standard O-ring fits the plug"
    decisions.append({
        "title": "Bore diameter", "choice": f"{bore:g} mm", "reason": why_bore,
        "requirements": [], "numbers": {"bore_mm": bore, "bay_diagonal_mm": round(diagonal, 3)},
    })
    decisions.append({
        "title": "O-ring", "choice": f"{ring_id:g} x {cs:g} mm {table['material']}",
        "reason": (f"smallest standard cross-section that stretches {stretch * 100:.1f} % over "
                   f"the groove root (limit {ring_cfg['stretch_max'] * 100:g} %) and squeezes "
                   f"{ring_cfg['squeeze'] * 100:g} % in the bore"),
        "requirements": [],
        "numbers": {"id_mm": ring_id, "cs_mm": cs, "stretch_percent": round(stretch * 100, 3),
                    "squeeze_percent": ring_cfg["squeeze"] * 100},
        "alternatives": alternatives,
    })

    # 3. Pressure and wall.
    stress = [r for r in approved if r.verify.check == "stress_safety_factor"]
    depths = [(d, r) for r in stress if (d := parse_depth_m(r.statement)) is not None]
    if not depths:
        raise ValueError(
            "a pressure housing needs an approved stress_safety_factor requirement whose "
            "statement names the depth, for example 'at 50 m depth'"
        )
    depth, governing = max(depths, key=lambda pair: pair[0])
    factor, density = pack_scenarios(pack_dir)
    design_depth = depth * factor
    pressure = pressure_mpa(density, design_depth)
    allowable = float(structure["allowable_stress_mpa"]) * float(
        structure["printed_knock_down_factor"]
    )
    required_sf = max((r.verify.limit.min for r in stress
                       if r.verify.limit is not None and r.verify.limit.min is not None),
                      default=1.0)
    margin = cfg["stress_margin"]
    headroom = allowable / (required_sf * margin) - pressure
    if headroom <= 0:
        raise ValueError(
            f"{pressure:.2f} MPa at {design_depth:g} m exceeds what printed PETG carries at "
            f"safety factor {required_sf:g}"
        )
    hoop = pressure * bore / 2 / headroom
    buckling = _buckling_wall_mm(
        bore, pressure, cfg["buckling_safety"], float(structure["youngs_modulus_mpa"]),
        float(structure["poisson_ratio"]), cfg["wall_step_mm"],
    )
    wall_reqs = [(r.verify.limit.min, r) for r in approved
                 if r.verify.check == "wall_thickness_min" and r.verify.limit is not None
                 and r.verify.limit.min is not None]
    wall_min = max((v for v, _ in wall_reqs), default=0.0)
    wall = _round_up(max(wall_min, cfg["min_wall_mm"], hoop, buckling), cfg["wall_step_mm"])
    governs = max(
        (("requirement minimum", wall_min), ("printing minimum", cfg["min_wall_mm"]),
         ("hoop stress", hoop), ("collapse", buckling)), key=lambda pair: pair[1])[0]
    decisions.append({
        "title": "Design pressure", "choice": f"{pressure:.3f} MPa at {design_depth:g} m",
        "reason": (f"{governing.id} names {depth:g} m; the pack's red-team scenarios add a "
                   f"depth factor of {factor:g} and a water density of {density:g} kg/m3"),
        "requirements": [governing.id],
        "numbers": {"depth_m": depth, "depth_factor": factor, "design_depth_m": design_depth,
                    "density_kg_m3": density, "pressure_mpa": round(pressure, 6)},
    })
    decisions.append({
        "title": "Wall thickness", "choice": f"{wall:g} mm",
        "reason": (f"{governs} governs: hoop stress needs {hoop:.2f} mm at safety factor "
                   f"{required_sf:g} x {margin:g} margin, long-tube collapse needs "
                   f"{buckling:.2f} mm at {cfg['buckling_safety']:g} x pressure"),
        "requirements": sorted({r.id for _, r in wall_reqs} | {governing.id}),
        "numbers": {"wall_mm": wall, "hoop_mm": round(hoop, 4), "collapse_mm": buckling,
                    "wall_min_mm": wall_min, "allowable_mpa": allowable},
    })

    # 4. Plug, flange and tube.
    groove_width = _round_up(ring_cfg["groove_width_ratio"] * cs, 0.1)
    land = ring_cfg["land_mm"]
    plug_depth = _round_up(groove_width + 2 * land, 0.5)
    flange = _round_up(cfg["flange_ratio"] * wall, cfg["wall_step_mm"])
    cavity = length + 2 * cfg["axial_clearance_mm"]
    tube = cavity + 2 * plug_depth
    decisions.append({
        "title": "Groove", "choice": f"{groove_width:g} wide x {groove_depth:.2f} deep mm",
        "reason": (f"width {ring_cfg['groove_width_ratio']:g} x the {cs:g} mm cross-section, "
                   f"depth {1 - ring_cfg['squeeze']:g} x cross-section minus the {gap:g} mm fit "
                   f"gap, with {land:g} mm lands either side"),
        "requirements": [], "numbers": {"groove_width_mm": groove_width,
                                        "groove_depth_mm": round(groove_depth, 4)},
    })
    decisions.append({
        "title": "End cap", "choice": f"{flange:g} mm flange, {plug_depth:g} mm plug",
        "reason": (f"the flange is {cfg['flange_ratio']:g} x the wall so it seats on the tube "
                   "end; the plug holds the groove and both lands"),
        "requirements": [], "numbers": {"flange_mm": flange, "plug_depth_mm": plug_depth},
    })
    decisions.append({
        "title": "Tube length", "choice": f"{tube:g} mm",
        "reason": (f"the {length:g} mm bay plus {cfg['axial_clearance_mm']:g} mm each end, plus "
                   f"a {plug_depth:g} mm plug at both ends"),
        "requirements": [], "numbers": {"tube_length_mm": tube, "cavity_mm": cavity,
                                        "bay_length_mm": length},
    })

    # 5. Cable gland boss, when the pack asks for one and it fits the cap.
    gland = cfg.get("gland", {})
    boss_wall = max(wall, 2.5)
    boss_d = gland.get("thread_mm", 12.5) + 2 * boss_wall
    want_gland = bool(cfg.get("cable_gland")) and boss_d <= bore
    decisions.append({
        "title": "Cable gland boss",
        "choice": f"{boss_d:g} mm boss for a {gland.get('thread_mm', 12.5):g} mm gland"
        if want_gland else "none",
        "reason": ("the pod leads its sensor cable out through the rear cap; the boss wall is "
                   f"{boss_wall:g} mm" if want_gland else
                   ("the pack does not ask for a cable gland" if not cfg.get("cable_gland")
                    else f"a {boss_d:g} mm boss does not fit a {bore:g} mm cap")),
        "requirements": [], "numbers": {"boss_diameter_mm": boss_d if want_gland else 0.0},
    })

    outer = round(bore + 2 * wall, 6)
    params = {
        "outer_diameter_mm": outer,
        "bore_diameter_mm": bore,
        "wall_thickness_mm": wall,
        "tube_length_mm": round(tube, 6),
        "flange_thickness_mm": flange,
        "plug_depth_mm": plug_depth,
        "fit_gap_mm": gap,
        "o_ring_id_mm": ring_id,
        "o_ring_cs_mm": cs,
        "groove_depth_mm": round(groove_depth, 6),
        "groove_width_mm": groove_width,
        "groove_land_mm": land,
        "battery_bay_mm": [length, width, height],
        "cable_gland": want_gland,
        "gland_hole_mm": float(gland.get("thread_mm", 12.5)),
        "boss_diameter_mm": round(boss_d, 6) if want_gland else float(boss_d),
        "boss_height_mm": float(gland.get("boss_height_mm", 7.0)),
        "gland_mass_g": float(gland.get("mass_g", 0.0)) if want_gland else 0.0,
        "design_depth_m": design_depth,
        "design_pressure_mpa": round(pressure, 6),
        "infill_factor": 1.0,
    }
    ex, ey, ez = contract.budgets.mechanical.outer_envelope_mm
    total = tube + 2 * flange + (params["boss_height_mm"] if want_gland else 0.0)
    if outer > min(ex, ey) or total > ez:
        raise PodEnvelopeError([outer, outer, round(total, 6)], [ex, ey, ez])
    decisions.append({
        "title": "Overall size", "choice": f"{outer:g} mm diameter x {total:g} mm long",
        "reason": f"inside the {ex:g} x {ey:g} x {ez:g} mm mechanical envelope",
        "requirements": [], "numbers": {"outer_diameter_mm": outer, "length_mm": total,
                                        "envelope_xy_mm": min(ex, ey), "envelope_z_mm": ez},
    })
    return params, decisions


# --- geometry --------------------------------------------------------------------------------


def _cap(p: Params, *, bonded: bool, with_gland: bool) -> Shape:
    """A cap in its own frame: flange on z in [0, flange], plug above it, boss below z = 0."""
    plug_od = p.bore_diameter_mm if bonded else p.plug_od_mm
    flange, depth = p.flange_thickness_mm, p.plug_depth_mm
    cap = Cylinder(p.outer_diameter_mm / 2, flange, align=BOTTOM)
    plug = Pos(0, 0, flange) * Cylinder(plug_od / 2, depth, align=BOTTOM)
    if not bonded:
        end = flange + depth
        plug = chamfer(plug.edges().filter_by_position(Axis.Z, end - 1e-6, end + 1e-6), LEAD_IN_MM)
    cap += plug
    # Groove: an annulus cut a land's width short of the free end of the plug.
    z = flange + depth - p.groove_land_mm - p.groove_width_mm
    ring = Cylinder(plug_od / 2 + 1, p.groove_width_mm, align=BOTTOM) - Cylinder(
        p.groove_root_diameter_mm / 2, p.groove_width_mm, align=BOTTOM
    )
    cap -= Pos(0, 0, z) * ring
    if with_gland and p.cable_gland:
        boss = Pos(0, 0, -p.boss_height_mm) * Cylinder(
            p.boss_diameter_mm / 2, p.boss_height_mm, align=BOTTOM
        )
        cap += boss
        if not bonded:
            hole = Pos(0, 0, -p.boss_height_mm) * Cylinder(
                p.gland_hole_mm / 2, p.boss_height_mm + flange + depth, align=BOTTOM
            )
            cap -= hole
    return cap


def _tube(p: Params) -> Shape:
    outer = Cylinder(p.outer_diameter_mm / 2, p.tube_length_mm, align=BOTTOM)
    return outer - Cylinder(p.bore_diameter_mm / 2, p.tube_length_mm, align=BOTTOM)


def _placed(p: Params, bonded: bool) -> dict[str, Shape]:
    lift = p.boss_offset_mm
    rear = Pos(0, 0, lift) * _cap(p, bonded=bonded, with_gland=True)
    front = Pos(0, 0, lift + p.total_length_mm - p.boss_offset_mm) * (
        Rot(X=180) * _cap(p, bonded=bonded, with_gland=False)
    )
    tube = Pos(0, 0, lift + p.flange_thickness_mm) * _tube(p)
    return {"tube": tube, "cap_rear": rear, "cap_front": front}


def build(params: Params) -> dict[str, Shape]:
    """The printed parts, in assembled position: tube, rear cap (with the boss), front cap."""
    parts = _placed(params, bonded=False)
    for name, part in parts.items():
        part.label = name
        if not part.is_valid or len(part.solids()) != 1:
            raise ValueError(f"{name} did not produce one connected solid")
    return parts


def analysis_solids(params: Params) -> dict[str, Shape]:
    """The vessel the structure solver meshes: tube and caps bonded into one solid, the gland
    hole filled, the O-ring grooves left as dry voids."""
    parts = _placed(params, bonded=True)
    vessel = parts["tube"] + parts["cap_rear"] + parts["cap_front"]
    vessel.label = "vessel"
    if not vessel.is_valid or len(vessel.solids()) != 1:
        raise ValueError("The bonded pressure vessel is not one connected solid")
    return {"vessel": vessel}


# --- what the mechanical interface publishes ------------------------------------------------


def hardware(params: Params, pack: dict | None = None) -> dict[str, float]:
    """Mass in grams of the bought parts: two O-rings (volume x table density) and the gland."""
    _, table = load_o_rings()
    ring = o_ring_mass_g(params.o_ring_id_mm, params.o_ring_cs_mm, table["density_g_cm3"])
    items = {"o_ring_rear": ring, "o_ring_front": ring}
    if params.cable_gland:
        items["cable_gland"] = params.gland_mass_g
    return {name: round(mass, 6) for name, mass in items.items()}


def extra_facts(params: Params) -> dict:
    p = params
    lift = p.boss_offset_mm
    cavity_z0 = lift + p.flange_thickness_mm + p.plug_depth_mm
    cavity_z1 = cavity_z0 + p.cavity_length_mm
    radius = p.outer_diameter_mm / 2
    area = math.pi * radius**2
    displaced = area * (p.total_length_mm - lift)
    if p.cable_gland:
        displaced += math.pi * (p.boss_diameter_mm / 2) ** 2 * p.boss_height_mm
    cavity = math.pi * (p.bore_diameter_mm / 2) ** 2 * p.cavity_length_mm
    walls = [p.wall_thickness_mm, p.flange_thickness_mm]
    if p.cable_gland:
        walls.append((p.boss_diameter_mm - p.gland_hole_mm) / 2)
    root = p.groove_root_diameter_mm / 2
    bore = p.bore_diameter_mm / 2
    regions = [[0.0, p.bore_diameter_mm / 2, round(cavity_z0, 6), round(cavity_z1, 6)]]
    # One groove per cap, a land short of the plug's free end (the rear one below the
    # cavity, the front one above it).
    for z0 in (cavity_z0 - p.groove_land_mm - p.groove_width_mm, cavity_z1 + p.groove_land_mm):
        regions.append([round(root, 6), round(bore, 6), round(z0, 6),
                        round(z0 + p.groove_width_mm, 6)])
    return {
        "outer_diameter_mm": p.outer_diameter_mm,
        "wall_min_mm": round(min(walls), 6),
        "displaced_volume_cm3": round(displaced / 1000, 6),
        "internal_volume_cm3": round(cavity / 1000, 6),
        "battery_bay_mm": list(p.battery_bay_mm),
        "battery_bay_centre_mm": [0.0, 0.0, round((cavity_z0 + cavity_z1) / 2, 6)],
        "design_depth_m": p.design_depth_m,
        "design_pressure_mpa": p.design_pressure_mpa,
        "o_ring": {"id_mm": p.o_ring_id_mm, "cs_mm": p.o_ring_cs_mm},
        "cable_gland": p.cable_gland,
        # The structure solver reads these three: which STEP to mesh, the outer radius, and
        # the regions (r_min, r_max, z_min, z_max) that are dry, so it never loads them.
        "structure_step": "vessel.step",
        "structure_dry_regions_mm": regions,
        "structure_outer_radius_mm": radius,
    }


def params_dict(params: Params) -> dict:
    return asdict(params)
