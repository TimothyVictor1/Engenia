"""Validated, offline parts catalogue. Numeric facts carry field-level provenance."""

from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DUTY_MODES = ("sample", "transmit", "sleep")
FIXED_FUNCTIONS = ("battery", "regulator")  # never driven by the duty-cycle power model
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")


class PartFact(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    id: str = Field(min_length=1)
    function: str = Field(pattern=r"[a-z][a-z0-9_]*")
    approved: Literal[True]
    mass_g: float = Field(gt=0)
    size_mm: tuple[float, float, float]
    supply_voltage_v: tuple[float, float]
    active_current_ma: float = Field(ge=0)
    sleep_current_ma: float = Field(ge=0)
    capacity_mah: float | None = Field(default=None, gt=0)
    nominal_voltage_v: float | None = Field(default=None, gt=0)
    output_voltage_v: float | None = Field(default=None, gt=0)
    source: str = Field(min_length=1)
    sources: dict[str, str]

    @model_validator(mode="after")
    def validate_facts(self) -> PartFact:
        if not self.source.strip() or not re.search(r"\bpp?\.?\s*\d", self.source):
            raise ValueError(f"{self.id}: source must name a datasheet and page")
        numeric = {
            "mass_g",
            "size_mm",
            "supply_voltage_v",
            "active_current_ma",
            "sleep_current_ma",
        }
        numeric.update(
            key
            for key in ("capacity_mah", "nominal_voltage_v", "output_voltage_v")
            if getattr(self, key) is not None
        )
        for key in numeric:
            if not self.sources.get(key, "").strip():
                raise ValueError(f"{self.id}: missing numeric source for {key}")
        if any(x <= 0 for x in self.size_mm):
            raise ValueError("size_mm must be positive")
        lo, hi = self.supply_voltage_v
        if not 0 < lo <= hi:
            raise ValueError("invalid supply voltage range")
        if self.function == "battery":
            if self.capacity_mah is None or self.nominal_voltage_v is None:
                raise ValueError("battery needs capacity and nominal voltage")
            if not lo <= self.nominal_voltage_v <= hi:
                raise ValueError("nominal battery voltage outside supply range")
        if self.function == "regulator" and self.output_voltage_v is None:
            raise ValueError("regulator needs output voltage")
        return self


class DesignProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    duty_cycle: dict[str, float]
    # Per function, the duty modes in which it draws active_current_ma; absent entries
    # and modes draw sleep_current_ma. Battery and regulator are modelled separately.
    active_modes: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "microcontroller": ["sample", "transmit"],
            "lora": ["transmit"],
            "audio": list(DUTY_MODES),
        }
    )
    regulator_efficiency: float = Field(gt=0, le=1)
    usable_capacity_fraction: float = Field(gt=0, le=1)
    board_mm: tuple[float, float, float]
    support_mass_g: float = Field(ge=0)
    support_current_ma: float = Field(ge=0)
    assembly_clearance_mm: float = Field(ge=0)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profile(self) -> DesignProfile:
        if set(self.duty_cycle) != {"sample", "transmit", "sleep"}:
            raise ValueError("duty_cycle must specify sample, transmit and sleep")
        if any(not math.isfinite(x) or not 0 <= x <= 1 for x in self.duty_cycle.values()):
            raise ValueError("duty fractions must be finite and between zero and one")
        if not math.isclose(sum(self.duty_cycle.values()), 1, abs_tol=1e-12):
            raise ValueError("duty fractions must sum to one")
        for function, modes in self.active_modes.items():
            if not _IDENTIFIER.fullmatch(function):
                raise ValueError(f"active_modes: {function!r} is not a part function name")
            unknown = sorted(set(modes) - set(DUTY_MODES))
            if unknown:
                raise ValueError(f"active_modes[{function}]: unknown duty modes {unknown}")
            if len(set(modes)) != len(modes):
                raise ValueError(f"active_modes[{function}]: repeated duty mode")
        regulated = {"battery", "regulator"} & set(self.active_modes)
        if regulated:
            raise ValueError(f"active_modes must not name {sorted(regulated)}")
        if any(x <= 0 for x in self.board_mm) or not self.source.strip():
            raise ValueError("profile needs positive board dimensions and a source")
        return self


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    design: DesignProfile
    parts: list[PartFact]

    @model_validator(mode="after")
    def validate_catalogue(self) -> Facts:
        ids = [p.id for p in self.parts]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate part id")
        required = required_functions(self)
        available = {p.function for p in self.parts}
        missing = sorted(required - available)
        if missing:
            raise ValueError(f"missing parts for {missing}")
        return self


def required_functions(facts: Facts) -> frozenset[str]:
    """Every function the pack's design needs: the powered ones plus battery and regulator."""
    return frozenset(facts.design.active_modes) | set(FIXED_FUNCTIONS)


def load_facts(path: Path) -> Facts:
    with path.open("rb") as stream:
        return Facts.model_validate(tomllib.load(stream))
