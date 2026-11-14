"""Generated mechanical source: all dimensions in mm."""
from pathlib import Path

import yaml

from packs.bracket.templates.bracket import (
    Params,
    analysis_solids,
    build,
    extra_facts,
    hardware,
)

params = Params(**yaml.safe_load(Path(__file__).with_name("params.yaml").read_text()))
parts = build(params)
analysis = analysis_solids(params)
hardware_g = hardware(params)
extra = extra_facts(params)

contract_version = 2
template = 'bracket'
