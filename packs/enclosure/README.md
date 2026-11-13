# packs/enclosure

Product pack for a deck-mounted sensor box (temperature, humidity, vibration). Same file
shape as `packs/buoy/`: a `pack.yaml` (materials, printing, templates), a build123d
template under `templates/`, and no parts catalogue yet.

- `pack.yaml` declares the `deck_enclosure` template and the checks it is measured with.
- `templates/deck_enclosure.py` builds a rectangular gasketed enclosure
  (`DeckEnclosureParams`, `build_deck_enclosure -> (body, lid)`), with the same parameter
  names the mechanical stage proposes, so a per-project pack hook can wire it in without
  renaming anything.

## Status (honest)

Nothing in `engenia/` reads this pack yet. The mechanical stage resolves
`packs/buoy/pack.yaml`, `packs/buoy/parts/facts.toml` and `packs/buoy/templates/*` by
hard-coded path, and the architect falls back to `packs/buoy/default_contract.json`, so
`projects/deck-sensor` currently builds with the buoy hull template and the buoy parts
catalogue. This pack is the ready-to-wire replacement; making the stages pack-aware is a
change for the seats that own those stages, not something this pull request patches.
