"""Generated mechanical source: all dimensions in mm."""
from pathlib import Path

import yaml

from packs.buoy.templates.hull import HullParams, build_hull

params = HullParams(**yaml.safe_load(Path(__file__).with_name("params.yaml").read_text()))
hull, lid = build_hull(params)

contract_version = 2
template = 'hull'
