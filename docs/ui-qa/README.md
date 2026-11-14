# Dashboard visual QA

Every view (Mission Control, Pipeline, Requirements, Design, Contract, Scenarios, Files, Live studio)
was checked with headless Chromium (Playwright) at 1920x1080 and 1280x720, in light, dark and
projector themes, with a real run loaded (`harbour-listener`, `deck-sensor`, both run in deterministic
mode) and in sample mode. `after/` keeps the 8 most useful shots (`harbour-listener`, 1920x1080,
compressed JPEG, under 2 MB in total): Mission Control in light, dark and projector, and the
Pipeline, Requirements, Design, Contract and Live studio views in light. The rest are not stored:
`shoot.mjs` regenerates every combination (see Reproduce). Mission Control now draws its graph from
the live `phase_start`/`phase_end` events and shows the real agent registry from `/api/agents`.

## What was fixed

| Problem seen | Fix |
|---|---|
| Mission page scrolled sideways by about 40 px (an invisible tooltip stuck out of the right edge) | Horizontal overflow is clipped on the page shell |
| Every graph node said "started" for a finished run (a real server sends only start/done messages, no phases) | Stage status from the server only when a run has no phase events at all; otherwise live phases decide |
| Graph shrank to 63% and was unreadable; node text tiny | Graph height follows the window; larger stage names and status text |
| Graph zoom buttons covered the last stage; lane label overlapped a repair arc | Buttons moved to the bottom-right; lane label raised |
| Dark theme: graph canvas, sidebar and 3D viewer stayed light, logo text unreadable | Dark `--surface` token; 3D canvas is transparent so the themed grid shows through |
| Long numbers: 191.872, 7.45547, 0.010204, 22.7873 | Rounded to 4 significant figures everywhere |
| Numbers without units in Requirements, Design, Contract, Scenarios | Units are shown (from the requirement, or from the fact name: `_mm`, `_g`, `_mw`, `_wh`, `_days`, `_c`, ...); dimensions read `114 x 114 x 43.2 mm` |
| Requirement ids wrapped (`R-` / `001`) | Never wrap |
| Scenario extras (`air_temp_c`, `wind_speed_ms`) misaligned with the rows above | Same two-column layout, right-aligned, with units |
| Design page showed only a lid for deck-sensor (it has `body.stl`, not `hull.stl`) | Loads `hull.stl`, `body.stl`, `lid.stl`, skipping what a project lacks |
| Fact names broke mid-word in the narrow column | Break only after `_` or `.`; side panel widened |
| Live studio: "Not built yet" 3D box even though the mechanical stage had a preview; `verify_plan#1` ran into its text; last timeline bar spilled out of the track | Uses the preview event's `stl` URL; wider label column; bars clamped to the track |
| Verification-layer strip wrapped into three uneven rows at 1280 | BEFORE and DURING side by side, AFTER below; one row from 1800 px |
| Two "Sample data" labels in the header | The checkbox reads "Use sample data" |
| Spinner flash on fast loads | Loading indicators fade in after 250 ms |

## Projector theme

A "Projector theme" button in the header (and in presentation mode) switches to a white background,
near-black text (about 17:1), muted text above 7:1, 1.5 px borders, darker status colours and type
about 18% larger. Also `?theme=projector`. The choice is remembered in the browser; pressing the
button again returns to the light or dark theme the system asked for.

## Reproduce

```
uv run engenia run harbour-listener && uv run engenia run deck-sensor
uv run python -m engenia.dashboard --project harbour-listener --port 8081 &
uv run python -m engenia.dashboard --project deck-sensor --port 8082 &
npm i playwright && npx playwright install chromium-headless-shell
node docs/ui-qa/shoot.mjs out light,dark,projector 1920x1080,1280x720  # writes PNGs to ./out
```
