"""Generated mechanical source: all dimensions in mm."""
from pathlib import Path

import yaml

from packs.enclosure.templates.deck_enclosure import DeckEnclosureParams, build_deck_enclosure

params = DeckEnclosureParams(**yaml.safe_load(Path(__file__).with_name("params.yaml").read_text()))
body, lid = build_deck_enclosure(params)

contract_version = 2
template = 'deck_enclosure'
