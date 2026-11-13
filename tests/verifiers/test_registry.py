from __future__ import annotations

import pytest

from verifiers import REGISTRY, available, load, register

EXPECTED = {
    "mass_budget",
    "buoyancy_margin",
    "print_bed_fit",
    "wall_thickness_min",
    "metacentric_height",
    "power_budget",
    "battery_capacity",
    "erc_clean",
    "bay_fit",
}


def test_registry_contains_every_builtin_verifier():
    assert EXPECTED <= set(REGISTRY)
    assert EXPECTED <= set(available())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_load_returns_run_callable(name):
    assert callable(load(name))


def test_load_unknown_name_raises_key_error():
    with pytest.raises(KeyError, match="unknown verifier"):
        load("not_a_verifier")


def test_register_adds_a_callable():
    def custom(ctx):
        return None

    assert register("test_custom_verifier", custom) is custom
    assert load("test_custom_verifier") is custom
